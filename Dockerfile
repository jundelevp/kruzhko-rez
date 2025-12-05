FROM python:3.10-slim

RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app

COPY requirements.txt bot.py ./

RUN pip install -r requirements.txt

CMD ["python", "bot.py"]
