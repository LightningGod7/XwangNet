FROM lscr.io/linuxserver/webtop:latest

# Install system packages

RUN apk update && apk upgrade
RUN apk add python3 py3-pip git netcat-openbsd rlwrap
RUN apk info  # List installed packages
