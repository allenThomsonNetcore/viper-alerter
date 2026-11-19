FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /home

ADD requirements.txt /home/

RUN pip install -r requirements.txt --no-cache-dir --compile

ADD s3_fetch/s3_downloader.py /home/s3_fetch/
ADD trigger_workflow/trigger_workflow.py trigger_workflow/ci.py /home/trigger_workflow/
ADD diff/diff.py /home/diff/
ADD platform_feed_validator/*.py /home/platform_feed_validator/
ADD config_handler/*.py /home/config_handler/
COPY exit_handler /home/exit_handler

# Add SFTP Cleanup plugin
COPY sftp_cleanup /home/sftp_cleanup

# Catalog indexing alert plugin
COPY catalog_indexing_alert /home/catalog_indexing_alert

RUN mkdir -p /mnt/out
RUN mkdir -p /tmp/s3 /tmp/process

RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

EXPOSE 5000

CMD ["python", "platform_feed_validator/app.py"]
