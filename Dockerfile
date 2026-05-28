FROM python:3.12-slim
RUN pip install flask gunicorn werkzeug -i https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /app
COPY app.py i18n_backend.py /app/
COPY static /app/static/
RUN mkdir -p /app/expense-imgs
ENV EXPENSE_IMG_DIR=/app/expense-imgs
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8600", "app:app"]
