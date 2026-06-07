FROM python:3.12-slim
RUN pip install flask gunicorn werkzeug Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /app
COPY requirements.txt /app/
RUN pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY app.py i18n_backend.py /app/
COPY static /app/static/
RUN mkdir -p /app/expense-imgs
ENV EXPENSE_IMG_DIR=/app/expense-imgs
ENV BG_DIR=/app/user-images
# Single worker — SQLite + multiple writers = SQLITE_BUSY under concurrency.
# Rate limiting is also per-process (in-memory dict), so -w 1 keeps it effective.
# Bumped to 2 workers: PDF generation takes 10-30 seconds and blocks all requests
# on a single worker. Two workers allows at least one request to be served during
# PDF generation. SQLite WAL mode handles concurrent reads safely.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8600", "--timeout", "90", "app:app"]
