FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libcairo2 libffi-dev shared-mime-info fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
RUN pip install flask gunicorn werkzeug Pillow weasyprint -i https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /app
COPY app.py i18n_backend.py /app/
COPY templates /app/templates/
COPY static /app/static/
RUN mkdir -p /app/expense-imgs
ENV EXPENSE_IMG_DIR=/app/expense-imgs
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8600", "app:app"]
