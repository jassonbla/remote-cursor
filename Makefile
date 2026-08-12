.PHONY: run test check

run:
	python3 -m remote_cursor.server

test:
	python3 -m unittest discover -v

check: test
	node --check remote_cursor/static/app.js
	python3 -m py_compile remote_cursor/*.py tests/*.py

