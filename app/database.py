import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any
from contextlib import contextmanager

from .config import DATABASE_PATH


@contextmanager
def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE NOT NULL,
                amount INTEGER NOT NULL,
                amount_rub INTEGER NOT NULL,
                order_number TEXT,
                delivery_address TEXT,
                client_tg_id INTEGER,
                client_username TEXT,
                client_name TEXT,
                client_phone TEXT,
                creator_tg_id INTEGER NOT NULL,
                status TEXT DEFAULT 'created',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                paid_at DATETIME,
                description TEXT,
                is_group INTEGER DEFAULT 0,
                orders_data TEXT
            )
        """)
        # Добавляем колонки, если они отсутствуют (для существующей БД)
        cursor.execute("PRAGMA table_info(invoices)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'is_group' not in columns:
            cursor.execute("ALTER TABLE invoices ADD COLUMN is_group INTEGER DEFAULT 0")
        if 'orders_data' not in columns:
            cursor.execute("ALTER TABLE invoices ADD COLUMN orders_data TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_id ON invoices(payment_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON invoices(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_tg_id ON invoices(client_tg_id)")
        conn.commit()


def save_invoice(data: Dict[str, Any]) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO invoices (
                payment_id, amount, amount_rub, order_number, delivery_address,
                client_tg_id, client_username, client_name, client_phone, creator_tg_id,
                description, is_group, orders_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["payment_id"],
            data["amount"],
            data["amount_rub"],
            data.get("order_number"),
            data.get("delivery_address"),
            data.get("client_tg_id"),
            data.get("client_username"),
            data.get("client_name"),
            data.get("client_phone"),
            data["creator_tg_id"],
            data.get("description"),
            data.get("is_group", 0),
            data.get("orders_data")
        ))
        conn.commit()
        return cursor.lastrowid


def get_invoice_by_payment_id(payment_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices WHERE payment_id = ?", (payment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_invoice_by_order_number(order_number: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices WHERE order_number = ?", (order_number,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_invoice_status(payment_id: str, status: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        paid_at = "CURRENT_TIMESTAMP" if status == "paid" else "NULL"
        cursor.execute(f"""
            UPDATE invoices 
            SET status = ?, paid_at = {paid_at}
            WHERE payment_id = ?
        """, (status, payment_id))
        conn.commit()
        cursor.execute("SELECT * FROM invoices WHERE payment_id = ?", (payment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_invoices(limit: int = 100) -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_invoices_by_client(client_tg_id: int) -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM invoices WHERE client_tg_id = ? ORDER BY created_at DESC", (client_tg_id,))
        return [dict(row) for row in cursor.fetchall()]