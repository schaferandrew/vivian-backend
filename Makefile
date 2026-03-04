shell:
	docker compose exec api python scripts/db_shell.py

sandbox:
	docker compose exec api python scripts/db_shell.py --sandbox
