import argparse
import os
import sys
import uvicorn
from dotenv import load_dotenv

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Run the SMM PRIME Production Application Server")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="Host IP to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")), help="Port to listen on (default: 8000)")
    parser.add_argument("--reload", action="store_true", default=(os.getenv("APP_ENV", "").lower() == "development"), help="Enable auto-reload for development")
    parser.add_argument("--workers", type=int, default=int(os.getenv("WEB_CONCURRENCY", "1")), help="Number of worker processes")

    args = parser.parse_args()

    print("==================================================")
    print(" 🚀 STARTING SMM PRIME SERVER")
    print(f" 🌐 Listening on: http://{args.host}:{args.port}")
    print(f" ⚙️ Mode: {'Development (auto-reload)' if args.reload else 'Production'}")
    print("==================================================")

    if args.reload:
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run("app.main:app", host=args.host, port=args.port, workers=args.workers)


if __name__ == "__main__":
    main()
