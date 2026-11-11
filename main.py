import base64, json, os
from datetime import date, timedelta
from typing import Optional
from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from data import FOODS, DEFAULT_FOOD, STORAGE_IDX, RECIPES

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./freshmate.db")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
TREE_KG = 12  # 一棵樹一年約吸收 12 kg CO2（估算）
URGENT_DAYS = 3

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
Session_ = sessionmaker(bind=engine)
Base = declarative_base()


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True)
    name = Column(String)
    grams = Column(Float)
    storage = Column(String)
    bought = Column(Date)
    expires = Column(Date)


class Event(Base):  # 每次用掉或丟棄都記一筆，成果由此彙總
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True)
    kind = Column(String)  # saved（快到期前用掉）/ eaten / wasted / cooked
    name = Column(String)
    grams = Column(Float, default=0)
    ntd = Column(Float, default=0)
    co2 = Column(Float, default=0)
    at = Column(Date, default=date.today)


Base.metadata.create_all(engine)
app = FastAPI(title="FreshMate API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])


def db():
    s = Session_()
    try:
        yield s
    finally:
        s.close()


def uid(x_user_id: str = Header(...)):
    """示範用：以 X-User-Id 區分使用者。正式版請改成 LINE Login / JWT 驗證。"""
    return x_user_id


def food(name):
    return FOODS.get(name, DEFAULT_FOOD)


def expiry(name, storage, bought):
    return bought + timedelta(days=food(name)[STORAGE_IDX[storage]])


def co2_of(name, g): return food(name)[3] * g / 1000
def ntd_of(name, g): return food(name)[4] * g / 1000
def days_left(i): return (i.expires - date.today()).days


class ItemIn(BaseModel):
    name: str
    grams: float = Field(gt=0)
    storage: str = "冷藏"
    bought: Optional[date] = None
    expires: Optional[date] = None  # 不填就依規則表推算（包裝有標示者請自行填入）


def out(i: Item):
    return {"id": i.id, "name": i.name, "grams": i.grams, "storage": i.storage,
            "bought": i.bought, "expires": i.expires, "days_left": days_left(i)}


@app.post("/items")
def add_item(b: ItemIn, u=Depends(uid), s: Session = Depends(db)):
    if b.storage not in STORAGE_IDX:
        raise HTTPException(422, "storage 必須是 冷藏／常溫／冷凍")
    bought = b.bought or date.today()
    i = Item(user_id=u, name=b.name, grams=b.grams, storage=b.storage, bought=bought,
             expires=b.expires or expiry(b.name, b.storage, bought))
    s.add(i); s.commit(); s.refresh(i)
    return out(i)


@app.get("/items")
def list_items(expiring_within: Optional[int] = None, u=Depends(uid), s: Session = Depends(db)):
    q = s.query(Item).filter_by(user_id=u).order_by(Item.expires)
    items = [out(i) for i in q]
    return [i for i in items if expiring_within is None or i["days_left"] <= expiring_within]


def consume(s, u, name, grams, wasted):
    """先到期先用（FIFO）。回傳實際扣掉的克數。"""
    need = grams
    for i in s.query(Item).filter_by(user_id=u, name=name).order_by(Item.expires):
        if need <= 0:
            break
        use = min(i.grams, need)
        i.grams -= use; need -= use
        kind = "wasted" if wasted else ("saved" if days_left(i) <= URGENT_DAYS else "eaten")
        s.add(Event(user_id=u, kind=kind, name=name, grams=use, ntd=ntd_of(name, use), co2=co2_of(name, use)))
        if i.grams <= 0:
            s.delete(i)
    return grams - need


@app.post("/items/{item_id}/use")
def use_item(item_id: int, wasted: bool = False, u=Depends(uid), s: Session = Depends(db)):
    i = s.query(Item).filter_by(id=item_id, user_id=u).first()
    if not i:
        raise HTTPException(404, "找不到這項食材")
    consume(s, u, i.name, i.grams, wasted); s.commit()
    return {"ok": True}


@app.delete("/items/{item_id}")
def delete_item(item_id: int, u=Depends(uid), s: Session = Depends(db)):
    n = s.query(Item).filter_by(id=item_id, user_id=u).delete(); s.commit()
    if not n:
        raise HTTPException(404, "找不到這項食材")
    return {"ok": True}


def stock(s, u):
    st = {}
    for i in s.query(Item).filter_by(user_id=u):
        d = st.setdefault(i.name, {"grams": 0, "min_left": 999})
        d["grams"] += i.grams; d["min_left"] = min(d["min_left"], days_left(i))
    return st


@app.get("/recipes/recommend")
def recommend(limit: int = 6, u=Depends(uid), s: Session = Depends(db)):
    st, res = stock(s, u), []
    for r in RECIPES:
        names = list(r["items"])
        have = [n for n in names if st.get(n, {}).get("grams", 0) >= r["items"][n]]
        miss = [n for n in names if n not in have]
        urgent = [n for n in have if st[n]["min_left"] <= URGENT_DAYS]
        co2 = sum(co2_of(n, g) for n, g in r["items"].items())
        cover = len(have) / len(names)
        score = cover * 50 + len(urgent) * 15 - len(miss) * 6 - co2 * 2
        if have:
            res.append({"name": r["name"], "items": r["items"], "missing": miss, "uses_expiring": urgent,
                        "co2_kg": round(co2, 2), "cookable": not miss, "score": round(score, 1)})
    return sorted(res, key=lambda x: -x["score"])[:limit]


@app.post("/recipes/{name}/cook")
def cook(name: str, u=Depends(uid), s: Session = Depends(db)):
    r = next((x for x in RECIPES if x["name"] == name), None)
    if not r:
        raise HTTPException(404, "沒有這道食譜")
    st = stock(s, u)
    miss = [n for n, g in r["items"].items() if st.get(n, {}).get("grams", 0) < g]
    if miss:
        raise HTTPException(409, f"食材不足：{'、'.join(miss)}")
    for n, g in r["items"].items():
        consume(s, u, n, g, False)
    s.add(Event(user_id=u, kind="cooked", name=name)); s.commit()
    return {"ok": True}


@app.get("/stats")
def stats(u=Depends(uid), s: Session = Depends(db)):
    def agg(kind):
        g, n, c = s.query(func.coalesce(func.sum(Event.grams), 0), func.coalesce(func.sum(Event.ntd), 0),
                          func.coalesce(func.sum(Event.co2), 0)).filter_by(user_id=u, kind=kind).one()
        return {"kg": round(g / 1000, 2), "ntd": round(n), "co2_kg": round(c, 2)}
    saved = agg("saved")
    return {"saved": saved, "wasted": agg("wasted"), "eaten": agg("eaten"),
            "cooked": s.query(Event).filter_by(user_id=u, kind="cooked").count(),
            "trees_equivalent": round(saved["co2_kg"] / TREE_KG, 2)}


PROMPT = ("這是一張冰箱或購物的照片。請辨識可見的食材，只回傳 JSON 陣列，不要其他文字或程式碼框。"
          '每個元素格式：{"name": 食材中文名, "grams": 估計克數, "storage": "冷藏"|"常溫"|"冷凍", "confidence": 0 到 1}。'
          "名稱請優先使用這些：" + "、".join(FOODS) + "。看不清楚的不要猜。")


@app.post("/recognize")
async def recognize(file: UploadFile = File(...), u=Depends(uid)):
    """辨識結果只是建議，前端必須讓使用者確認後再呼叫 POST /items 入庫。"""
    import anthropic  # 需設定 ANTHROPIC_API_KEY
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(415, "只支援 jpeg / png / webp")
    data = base64.b64encode(await file.read()).decode()
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=1000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": file.content_type, "data": data}},
            {"type": "text", "text": PROMPT}]}])
    text = "".join(b.text for b in msg.content if b.type == "text").replace("```json", "").replace("```", "").strip()
    try:
        found = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(502, "辨識結果格式錯誤，請重拍或手動輸入")
    today = date.today()
    for f in found:
        f["suggested_expires"] = expiry(f.get("name", ""), f.get("storage", "冷藏"), today) if f.get("storage") in STORAGE_IDX else None
    return found
