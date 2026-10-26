FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy only files required to run the website; local PDFs, images, and notes stay out.
COPY quiz_server.py pdf_quiz_site.html admin.html java_mcq_quiz.html ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8002

CMD ["python3", "quiz_server.py"]
