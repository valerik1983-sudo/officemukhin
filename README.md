# Telegram Payment Bot (Bothost + T‑Банк)

Бот для приема оплат через T‑Банк (Тинькофф) Эквайринг.

## Возможности

### Для клиентов
- Оформление заказа через кнопки
- Оплата по ссылке через T‑Банк
- Автоматическое уведомление менеджеров

### Для менеджеров
- Генерация ссылок для оплаты (команда `/link`)
- Проверка статуса платежа (команда `/check`)
- Уведомления о новых заказах и оплатах

## Установка на Bothost

1. Создайте репозиторий на GitHub и загрузите код
2. Зайдите в панель Bothost, создайте новый проект
3. Подключите GitHub репозиторий
4. В настройках проекта добавьте переменные окружения (см. `.env.example`)
5. Нажмите "Deploy"

## Локальная разработка

```bash
# Клонируем репозиторий
git clone https://github.com/your-username/telegram-payment-bot.git
cd telegram-payment-bot

# Создаем виртуальное окружение
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Устанавливаем зависимости
pip install -r requirements.txt

# Копируем .env.example в .env и заполняем
cp .env.example .env

# Запускаем
python -m app.main