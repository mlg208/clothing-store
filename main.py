import pandas as pd
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text
from typing import Optional

app = FastAPI()

#статика (CSS, JS, Картинки)
app.mount("/static", StaticFiles(directory="static"), name="static")

# настройка шаблонов Jinja2
templates = Jinja2Templates(directory="templates")

# подключение к PostgreSQL
# DATABASE_URL = "postgresql://postgres:1234@localhost:5432/brand_shop"
# engine = create_engine(DATABASE_URL)


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:1234@localhost:5432/brand_shop")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)


# --- маршрут фронта ---

@app.get("/")
async def home(request: Request):
    """Главная страница: Хиты и Новинки"""
    query_hits = text("SELECT * FROM products WHERE is_hit = TRUE LIMIT 4")
    query_new = text("SELECT * FROM products ORDER BY created_at DESC LIMIT 4")

    with engine.connect() as conn:
        hits_products = conn.execute(query_hits).mappings().all()
        new_products = conn.execute(query_new).mappings().all()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "hits": hits_products,
        "new_arrivals": new_products
    })


@app.get("/catalog/{gender}")
async def get_catalog(request: Request, gender: str):
    """Каталог по категориям"""
    query = text("SELECT * FROM products WHERE category = :g OR :g = 'all'")
    with engine.connect() as conn:
        products = conn.execute(query, {"g": gender}).mappings().all()

    return templates.TemplateResponse("catalog.html", {
        "request": request,
        "products": products,
        "gender": gender.capitalize()
    })


@app.get("/search")
async def search_products(
        request: Request,
        query: Optional[str] = None,
        brand: Optional[str] = None,
        size: Optional[str] = None,
        min_price: Optional[str] = None,
        max_price: Optional[str] = None
):
    try:
        final_min = int(min_price) if min_price and min_price.strip() else 0
        final_max = int(max_price) if max_price and max_price.strip() else 10000000
    except ValueError:
        final_min = 0
        final_max = 10000000

    # базовый запрос
    sql = "SELECT * FROM products WHERE price >= :min_price AND price <= :max_price"
    params = {"min_price": final_min, "max_price": final_max}

    if query and query.strip():
        sql += " AND (name ILIKE :query OR brand ILIKE :query)"
        params["query"] = f"%{query}%"

    if brand and brand.strip():
        sql += " AND brand = :brand"
        params["brand"] = brand


    if size and size.strip():
        sql += f" AND (stock_json ->> :size)::int > 0"
        params["size"] = size

    sql += " ORDER BY created_at DESC"

    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        products = result.mappings().all()

    return templates.TemplateResponse("search.html", {
        "request": request,
        "products": products,
        "search_query": query
    })


@app.get("/new-arrivals")
async def new_arrivals(request: Request):
    """Страница-заглушка для новых поступлений"""
    return templates.TemplateResponse("new_arrivals.html", {"request": request})


@app.get("/profile/{user_id}")
async def profile(request: Request, user_id: int):
    """Личный кабинет"""
    query = text("""
        SELECT o.id, p.name, o.size, o.status, o.created_at 
        FROM orders o 
        JOIN products p ON o.product_id = p.id 
        WHERE o.user_id = :u_id
        ORDER BY o.created_at DESC
    """)
    with engine.connect() as conn:
        orders = conn.execute(query, {"u_id": user_id}).mappings().all()

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "orders": orders,
        "user_id": user_id
    })


@app.get("/cart")
async def cart(request: Request):
    """Корзина"""
    return templates.TemplateResponse("cart.html", {"request": request})


# --- APIшки АНАЛИТИКИ ---

@app.get("/api/recommendations/{user_id}")
async def api_recommendations(user_id: int):
    """Аналитика Pandas: рекомендации на основе истории покупок"""
    try:
        query = f"SELECT p.brand FROM orders o JOIN products p ON o.product_id = p.id WHERE o.user_id = {user_id}"
        df = pd.read_sql(query, engine)

        if df.empty:
            return {"type": "general", "items": []}

        fav_brand = df['brand'].value_counts().idxmax()
        rec_query = text("SELECT id, name, price, brand FROM products WHERE brand = :b LIMIT 4")

        with engine.connect() as conn:
            items = conn.execute(rec_query, {"b": fav_brand}).mappings().all()

        return {"type": "personalized", "fav_brand": fav_brand, "items": items}
    except Exception as e:
        return {"error": str(e)}


@app.get("/checkout")
async def checkout(request: Request):
    """Страница выбора способа оплаты"""
    return templates.TemplateResponse("checkout.html", {"request": request})

@app.get("/payment-success")
async def payment_success(request: Request):
    """Страница успешной имитации оплаты"""
    return templates.TemplateResponse("success.html", {"request": request})
@app.get("/payment-card")
async def payment_card(request: Request):
    """Страница ввода данных банковской карты"""
    return templates.TemplateResponse("card_input.html", {"request": request})
@app.get("/payment-crypto")
async def payment_crypto(request: Request):
    """Страница оплаты криптовалютой"""
    return templates.TemplateResponse("crypto_input.html", {"request": request})


#история заказов
@app.post("/api/create-order")
async def create_order(payload: dict = Body(...)):
    """API для записи заказа в БД после 'оплаты'"""
    user_id = payload.get("user_id", 1)  # Пока фиксируем ID=1, патамушта нет системы логина
    product_id = payload.get("product_id")
    size = payload.get("size")

    if not product_id:
        raise HTTPException(status_code=400, detail="Product ID missing")

    query = text("""
        INSERT INTO orders (user_id, product_id, size, status, created_at)
        VALUES (:u_id, :p_id, :size, 'Оплачено', NOW())
    """)

    try:
        with engine.connect() as conn:
            conn.execute(query, {"u_id": user_id, "p_id": product_id, "size": size})
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}



#товары
@app.get("/product/{product_id}")
async def product_detail(request: Request, product_id: int):
    query = text("SELECT * FROM products WHERE id = :p_id")
    try:
        with engine.connect() as conn:
            result = conn.execute(query, {"p_id": product_id})
            product = result.mappings().first()

        if not product:
            return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

        product_dict = dict(product)

        if "description" not in product_dict or not product_dict["description"]:
            product_dict["description"] = "Описание этого премиального товара скоро появится."

        return templates.TemplateResponse("product_detail.html", {
            "request": request,
            "product": product_dict
        })
    except Exception as e:
        print(f"Ошибка БД: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при загрузке товара")

# Запуск через терминал: uvicorn main:app --reload
