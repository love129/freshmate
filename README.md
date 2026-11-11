# FreshMate API

## 啟動
本機（SQLite）：
    pip install -r requirements.txt
    uvicorn main:app --reload
Docker（PostgreSQL）：
    cp .env.example .env   # 填入 ANTHROPIC_API_KEY
    docker compose up --build

開啟 http://localhost:8000/docs 可直接試用所有 API。每個請求需帶 Header：X-User-Id: 任意字串。

## API
- POST /items、GET /items?expiring_within=3、POST /items/{id}/use?wasted=false、DELETE /items/{id}
- GET /recipes/recommend、POST /recipes/{name}/cook
- GET /stats
- POST /recognize（上傳照片，回傳辨識建議，需使用者確認後再 POST /items）

## 尚未完成
- 正式登入（目前用 X-User-Id 示範）
- LINE Bot 推播與電子發票載具串接（需申請 LINE channel 與財政部 API 資格）
- 資料庫遷移（Alembic）、單元測試、部署
- 食材與碳排資料換成官方資料集
