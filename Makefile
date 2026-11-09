# Shortcuts for running Musica with Docker. `make` alone starts it.
.PHONY: up down logs update restart

up:
	@test -f .env || (cp .env.example .env && echo "Created .env - set MUSICA_PASSWORD in it, then run make again." && exit 1)
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

restart:
	docker compose restart

update:
	git pull
	docker compose up -d --build
