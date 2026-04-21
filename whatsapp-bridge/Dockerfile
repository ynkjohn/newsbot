FROM node:20-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global url."https://github.com/".insteadOf "ssh://git@github.com/" \
    && git config --global url."https://github.com/".insteadOf "git@github.com:"

COPY package.json ./
RUN npm install --omit=dev

COPY index.js ./

RUN mkdir -p /app/auth_session

EXPOSE 3000

CMD ["node", "index.js"]
