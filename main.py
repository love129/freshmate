import json, os
from datetime import date, timedelta, datetime
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from passlib.context import CryptContext
from jose import JWTError, jwt
from data import FOODS, DEFAULT_FOOD, STORAGE_IDX, RECIPES

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./freshmate.db")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")  # 免費層可用；額度不夠可改 gemini-2.5-flash-lite
SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-it")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 預設7天

TREE_KG = 12  
URGENT_DAYS = 3

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
Session_ = sessionmaker(bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True)
    name = Column(String)
    grams = Column(Float)
    storage = Column(String)
    bought = Column(Date)
    expires = Column(Date)

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True)
    kind = Column(String)  # saved / eaten / wasted / cooked
    name = Column(String)
    grams = Column(Float, default=0)
    ntd = Column(Float, default=0)
    co2 = Column(Float, default=0)
    at = Column(Date, default=date.today)

Base.metadata.create_all(engine)
app = FastAPI(title="FreshMate API", version="2.0")

# 完整設定 CORS，允許所有來源（包含 GitHub Pages 與本地端）的跨域請求與憑證
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def db():
    s = Session_()
    try:
        yield s
    finally:
        s.close()

# 驗證使用者身分 (JWT)
def get_current_user(token: str = Depends(oauth2_scheme), s: Session = Depends(db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = s.query(User).filter_by(username=username).first()
    if user is None:
        raise credentials_exception
    return username

def food(name):
    return FOODS.get(name, DEFAULT_FOOD)

def expiry(name, storage, bought):
    return bought + timedelta(days=food(name)[STORAGE_IDX[storage]])

def co2_of(name, g): return food(name)[3] * g / 1000
def ntd_of(name, g): return food(name)[4] * g / 1000
def days_left(i): return (i.expires - date.today()).days

# --- 認證 API ---
class UserCreate(BaseModel):
    username: str
    password: str

@app.post("/auth/register")
def register(user_in: UserCreate, s: Session = Depends(db)):
    existing = s.query(User).filter_by(username=user_in.username).first()
    if existing:
        raise HTTPException(400, "使用者名稱已被註冊")
    hashed_pwd = pwd_context.hash(user_in.password)
    new_user = User(username=user_in.username, hashed_password=hashed_pwd)
    s.add(new_user)
    s.commit()
    return {"ok": True, "message": "註冊成功"}

@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), s: Session = Depends(db)):
    user = s.query(User).filter_by(username=form_data.username).first()
    if not user or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")
    
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": user.username, "exp": expire}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": encoded_jwt, "token_type": "bearer"}


# --- 食材與冰箱 API ---
class ItemIn(BaseModel):
    name: str
    grams: float = Field(gt=0)
    storage: str = "冷藏"
    bought: Optional[date] = None
    expires: Optional[date] = None

def out(i: Item):
    return {"id": i.id, "name": i.name, "grams": i.grams, "storage": i.storage,
            "bought": i.bought, "expires": i.expires, "days_left": days_left(i)}

@app.post("/items")
def add_item(b: ItemIn, u=Depends(get_current_user), s: Session = Depends(db)):
    if b.storage not in STORAGE_IDX:
        raise HTTPException(422, "storage 必須是 冷藏／常溫／冷凍")
    bought = b.bought or date.today()
    i = Item(user_id=u, name=b.name, grams=b.grams, storage=b.storage, bought=bought,
             expires=b.expires or expiry(b.name, b.storage, bought))
    s.add(i); s.commit(); s.refresh(i)
    return out(i)

@app.get("/items")
def list_items(expiring_within: Optional[int] = None, u=Depends(get_current_user), s: Session = Depends(db)):
    q = s.query(Item).filter_by(user_id=u).order_by(Item.expires)
    items = [out(i) for i in q]
    return [i for i in items if expiring_within is None or i["days_left"] <= expiring_within]

def consume(s, u, name, grams, wasted):
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
def use_item(item_id: int, wasted: bool = False, u=Depends(get_current_user), s: Session = Depends(db)):
    """把「這一筆」食材標記為已使用（用完）；wasted=true 則記為浪費。"""
    i = s.query(Item).filter_by(id=item_id, user_id=u).first()
    if not i:
        raise HTTPException(404, "找不到這項食材")
    kind = "wasted" if wasted else ("saved" if days_left(i) <= URGENT_DAYS else "eaten")
    s.add(Event(user_id=u, kind=kind, name=i.name, grams=i.grams,
                ntd=ntd_of(i.name, i.grams), co2=co2_of(i.name, i.grams)))
    s.delete(i)
    s.commit()
    return {"ok": True, "kind": kind}

@app.delete("/items/{item_id}")
def delete_item(item_id: int, u=Depends(get_current_user), s: Session = Depends(db)):
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
def recommend(limit: int = 6, u=Depends(get_current_user), s: Session = Depends(db)):
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
def cook(name: str, u=Depends(get_current_user), s: Session = Depends(db)):
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
def stats(u=Depends(get_current_user), s: Session = Depends(db)):
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

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

@app.post("/recognize")
async def recognize(file: UploadFile = File(...), u=Depends(get_current_user)):
    from google import genai
    from google.genai import types, errors

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(500, "伺服器未設定 GEMINI_API_KEY")

    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(415, "只支援 jpeg / png / webp 格式的圖片")

    contents = await file.read()
    if len(contents) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "圖片太大，請壓縮到 10 MB 以下")

    try:
        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model=MODEL,
            contents=[types.Part.from_bytes(data=contents, mime_type=file.content_type), PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
        )
        text = (resp.text or "").replace("```json", "").replace("```", "").strip()
        found = json.loads(text)
        if isinstance(found, dict):  # 偶爾模型會包成 {"items": [...]}
            found = next((v for v in found.values() if isinstance(v, list)), [])
        if not isinstance(found, list):
            raise ValueError("AI 回傳格式不是陣列")
    except errors.APIError as e:
        if e.code == 429:
            raise HTTPException(429, "今天的免費辨識額度用完了，請稍後再試，或改用 gemini-2.5-flash-lite")
        raise HTTPException(502, f"辨識失敗：{e.message}")
    except Exception as e:
        raise HTTPException(502, f"辨識失敗：{str(e)}")

    today = date.today()
    result = []
    for f in found:
        if not isinstance(f, dict):
            continue
        storage = f.get("storage") if f.get("storage") in STORAGE_IDX else "冷藏"
        f["storage"] = storage
        f["suggested_expires"] = expiry(f.get("name", ""), storage, today)
        result.append(f)
    return result
