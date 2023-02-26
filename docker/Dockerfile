FROM python:3.10-buster

ARG REPO_DIR

EXPOSE 8000

ARG version
ENV VERSION=$version

RUN pip install \
  pipenv

WORKDIR /app

COPY . /app

RUN pipenv install --deploy

ENTRYPOINT ["/usr/bin/dumb-init", "--"]

CMD pipenv run python MAIN.py
