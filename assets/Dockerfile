# Copyright (c) 2018 Anton Semjonov
# Licensed under the MIT License

FROM python:3.13.3-alpine

# default cron schedule
ENV SCHEDULE="*/15 * * * *"

# copy entrypoint script which execs crond
COPY assets/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/ash", "/entrypoint.sh"]
CMD ["cron", "/imapfetch.cfg"]

# copy application files and install
WORKDIR /app
COPY pyproject.toml README.md imapfetch.py ./
RUN pip install ./