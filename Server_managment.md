## Общая информация
---
Админка лежит по адресу:
	`/root/dlmm-sdk-mirror/account_service`

Серверное приложение:
	`/root/dlmm-liquid-dev/ts-client`

## Конфигурационный файл
---

Данный файл отвечает за проксирование запросов на сервер.

Адрес: `/etc/nginx/sites-available/keep-in-mind.conf`
Примерное содержание:
```bash
server {
    listen 80;
    listen [::]:80;

    server_name meteorahelper.ru;
    return 301 https://meteorahelper.ru$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    server_name meteorahelper.ru;

    # Проксирование запросов к /api/v1/admin на порт 8000
    location /admin {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Проксирование запросов к /api/v1/meteora на порт 3000
    location /api/v1/meteora {
        proxy_pass http://localhost:3000;
    }

    ssl_certificate /etc/letsencrypt/live/meteorahelper.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/meteorahelper.ru/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/meteorahelper.ru/chain.pem;

    include snippets/ssl-params.conf;
}
```

### Внесение изменений
Для активации внесенных изменений в конфигурационный файл выполнить:
```bash
# Проверка conf файлов на ошибки
sudo nginx -t

# Перезапуск nginx сервиса
sudo systemctl restart nginx
```


## Управление сервисами
---
На сервере запущены сервисы `account_service.service` и `meteora.service`.
- Запуск
	`sudo systemctl start <service_name>`
- Статус
	`sudo systemctl status <service_name>`
- Стоп
	`sudo systemctl stop <service_name>`


### Внесение изменений
#### Account_service

 ```bash
 nano /etc/systemd/system/account_service.service
 ```

##### Meteora
```
nano /etc/systemd/system/meteora.service
```
