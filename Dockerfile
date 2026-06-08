FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libcairo2 libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple flask gunicorn werkzeug
WORKDIR /app
COPY requirements.txt /app/
RUN pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY app.py i18n_backend.py /app/
COPY routes/ /app/routes/
COPY shared/ /app/shared/
COPY templates/ /app/templates/
COPY scripts/ /app/scripts/
COPY static /app/static/
RUN mkdir -p /app/expense-imgs /app/user-images /app/data /app/pdf_cache
ENV EXPENSE_IMG_DIR=/app/expense-imgs
ENV BG_DIR=/app/user-images
ENV DB=/app/data/snail.db
# Single worker — SQLite only supports one writer at a time.
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8600", "--timeout", "120", "app:app"]
