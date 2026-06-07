FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libcairo2 libffi-dev shared-mime-info fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
RUN pip install flask gunicorn werkzeug Pillow weasyprint -i https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /app
COPY app.py i18n_backend.py /app/
COPY shared/ /app/shared/
COPY routes/ /app/routes/
COPY templates/ /app/templates/
COPY static/ /app/static/
RUN mkdir -p /app/expense-imgs /app/user-images/avatars /app/user-images/covers
ENV EXPENSE_IMG_DIR=/app/expense-imgs
ENV BG_DIR=/app/user-images
# Single worker — SQLite + multiple writers = SQLITE_BUSY under concurrency.
# Rate limiting is also per-process (in-memory dict), so -w 1 keeps it effective.
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8600", "app:app"]
