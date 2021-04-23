# Copyright (c) 2018 Anton Semjonov
# Licensed under the MIT License

FROM python:alpine

# default cron interval
ENV MINUTES=15

# copy init script which execs crond
COPY assets/container/init.sh /init.sh
ENTRYPOINT ["/bin/ash", "/init.sh", "/usr/bin/imapfetch.py"]
CMD ["/config"]

# copy imapfetch script
COPY imapfetch/imapfetch.py /usr/bin/imapfetch.py
