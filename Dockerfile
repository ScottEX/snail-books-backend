FROM python:3.12-slim
RUN pip install flask gunicorn -i https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /app
COPY app.py /app/
COPY templates /app/templates/
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8600", "app:app"]
