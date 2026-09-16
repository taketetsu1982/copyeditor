FROM python:3.12.14-slim
COPY requirements.generated.txt /dependencies/requirements.generated.txt
RUN pip install --no-cache-dir -r /dependencies/requirements.generated.txt
WORKDIR /app
COPY src src
COPY rules rules
COPY examples examples
RUN mkdir -p /etc/copyeditor/rules.d
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-m", "copyeditor"]
