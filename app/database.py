import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any
from contextlib import contextmanager

from .config import DATABASE_PATH


@contextmanager
def get_connection():
    """Контекстный менеджер для работы с БД"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # Чтобы можно было обращаться по именам колонок
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Создает таблицы, если их нет"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Таблица счетов (инвойсов)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE NOT NULL,      -- ID платежа в T‑Банк (OrderId)
                amount INTEGER NOT NULL,              -- Сумма в КОПЕЙКАХ!
                amount_rub INTEGER NOT NULL,          -- Сумма в рублях (для отображения)
                order_number TEXT,                    -- Номер заказа (может быть NULL)
                delivery_address TEXT,                -- Адрес доставки (может быть NULL)
                client_tg_id INTEGER,                 -- Telegram ID клиента
                client_username TEXT,                 -- Username клиента
                creator_tg_id INTEGER NOT NULL,       -- Кто создал (клиент или менеджер)
                status TEXT DEFAULT 'created',        -- created | paid | expired | canceled
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                paid_at DATETIME,
                description TEXT                      -- Описание платежа
            )
        """)
        
        # Индексы для быстрого поиска
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_id ON invoices(payment_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON invoices(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_tg_id ON invoices(client_tg_id)")
        
        conn.commit()


def save_invoice(data: Dict[str, Any]) -> int:
    """
    Сохраняет новый счет в БД.
    
    Args:
        data: Словарь с данными счета
            - payment_id: ID платежа в T‑Банк
            - amount: Сумма в КОПЕЙКАХ
            - amount_rub: Сумма в рублях
            - order_number: Номер заказа (опционально)
            - delivery_address: Адрес доставки (опционально)
            - client_tg_id: Telegram ID клиента (опционально)
            - client_username: Username клиента (опционально)
            - creator_tg_id: Telegram ID создателя
            - description: Описание платежа
    
    Returns:
        ID созданной записи
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO invoices (
                payment_id, amount, amount_rub, order_number, delivery_address,
                client_tg_id, client_username, creator_tg_id, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["payment_id"],
            data["amount"],
            data["amount_rub"],
            data.get("order_number"),
            data.get("delivery_address"),
            data.get("client_tg_id"),
            data.get("client_username"),
            data["creator_tg_id"],
            data.get("description")
        ))
        conn.commit()
        return cursor.lastrowid


def get_invoice_by_payment_id(payment_id: str) -> Optional[Dict[str, Any]]:
    """Получает счет по ID платежа в T‑Банк"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices WHERE payment_id = ?", (payment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_invoice_by_order_number(order_number: str) -> Optional[Dict[str, Any]]:
    """Получает счет по номеру заказа"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices WHERE order_number = ?", (order_number,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_invoice_status(payment_id: str, status: str) -> Optional[Dict[str, Any]]:
    """Обновляет статус счета"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Обновляем статус
        paid_at = "CURRENT_TIMESTAMP" if status == "paid" else "NULL"
        cursor.execute(f"""
            UPDATE invoices 
            SET status = ?, paid_at = {paid_at}
            WHERE payment_id = ?
        """, (status, payment_id))
        conn.commit()
        
        # Возвращаем обновленную запись
        cursor.execute("SELECT * FROM invoices WHERE payment_id = ?", (payment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_invoices(limit: int = 100) -> list:
    """Получает последние счета (для админки)"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM invoices ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]


def get_invoices_by_client(client_tg_id: int) -> list:
    """Получает все счета клиента"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM invoices WHERE client_tg_id = ? ORDER BY created_at DESC",
            (client_tg_id,)
        )
        return [dict(row) for row in cursor.fetchall()]