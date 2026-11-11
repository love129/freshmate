# 食刻管家 FreshMate 前端（繁體中文版）

## 1. 放置檔案
將 `index.html` 放到你目前的專案資料夾：
`C:\Users\USER\freshmate-api\frontend\index.html`

## 2. 開啟後端 CORS
打開 `main.py`，在最上方 import 區域加入：
```python
from fastapi.middleware.cors import CORSMiddleware
```
找到 `app = FastAPI(...)`，在它的下一行加入：
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
儲存 main.py。若 uvicorn 使用 `--reload`，會自動重啟。

## 3. 啟動後端
在 VS Code 終端機：
```powershell
cd C:\Users\USER\freshmate-api
.venv\Scripts\Activate.ps1
uvicorn main:app --reload
```
保持這個終端機開啟。

## 4. 啟動中文網站
再開一個新的 VS Code 終端機：
```powershell
cd C:\Users\USER\freshmate-api\frontend
python -m http.server 5500
```
瀏覽器開啟：
http://127.0.0.1:5500

## 5. 前端使用的 API
目前依照先前對話中已測試的後端路徑串接：
- GET /items
- POST /items
- PUT /items/{id}（編輯）
- DELETE /items/{id}（刪除）
- GET /recipes/recommend
- POST /recipes/{recipe_name}/cook
- GET /stats
- POST /recognize（multipart/form-data，欄位名稱 file）

所有請求都會帶 `X-User-Id: test`，請沿用你之前測試資料時的 `test`。

## 6. 重要：先確認你的 main.py 路由
你之前提供的執行紀錄能確認新增食材、食譜推薦、煮菜、統計、照片辨識等路徑，但目前沒有附上完整 main.py 原始碼。因此編輯、刪除和照片上傳的欄位名稱可能需要依照你的實際程式調整。
如果網頁顯示 404，代表後端沒有對應路由；如果顯示 422，通常代表欄位名稱或資料格式不同。請把 `main.py` 上傳，我可以依照實際路由幫你精準對接。

## 7. 常見問題
- 網頁顯示「未連線」：確認 `uvicorn` 還在執行，並確認 `API_BASE` 是 `http://127.0.0.1:8000`。
- CORS 錯誤：確認已加入 CORS 程式碼，且網站網址是 `http://127.0.0.1:5500`。
- AI 辨識失敗：確認 `.env` 的 API 金鑰已正確設定，且 `main.py` 的辨識路由正常。
- 不要直接用檔案總管雙擊 index.html；請用 `python -m http.server 5500` 開啟網站。
