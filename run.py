import uvicorn
import os

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(
        "app.main:app",  # правильный путь к FastAPI приложению
        host="0.0.0.0",
        port=port,
        reload=False  # для продакшена лучше без reload
    )