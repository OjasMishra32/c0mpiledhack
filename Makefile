.PHONY: install dev demo test backend frontend clean ip

VENV := backend/.venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

install: ## one command to set everything up
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.txt
	cd frontend && npm install
	@test -f .env || cp .env.example .env
	@echo ""
	@echo "  HIVE installed. Run 'make dev' and open http://localhost:5173/host"
	@echo ""

dev: ## backend :8000 + frontend :5173
	@$(MAKE) -j2 backend frontend

backend:
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

frontend:
	cd frontend && npm run dev -- --host 0.0.0.0

demo: ## full demo mode: simulated workers, no camera or keys needed
	DEMO_MODE=true WORLD_MODE=simulation $(MAKE) dev

test:
	$(VENV)/bin/pytest backend/tests -q
	cd frontend && npm run test --if-present

ip: ## print the join URL for phones
	@$(PY) -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print('  join → http://%s:5173/join' % s.getsockname()[0]);s.close()"

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist
