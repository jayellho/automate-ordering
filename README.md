# Automate Ordering
This repository automates adding of orders into cart for ordering from nassaucandy.com given a list of orders.


## Installation - for Linux.
**EITHER: Open a terminal and run the following.**
```bash
chmod +x ./install-uv-playwright.sh
./install-uv-playwright.sh
```
**OR: Follow the manual setup below.**
<details>
<summary>Manual setup</summary>

Run the following in a terminal within your project directory.

1. Install `uv`.
```sh
wget -qO- https://astral.sh/uv/install.sh | sh
```
2. Setup `uv` virtual environment called `order-venv` and install playwright
```bash
uv venv order-venv
source order-venv/bin/activate
uv pip install pytest-playwright
uv pip install dotenv
playwright install
```
3. Fill in the below with your authentication details in `.env_default` and copy and paste into a `.env` file.
```
USER=<your-nassau-candy-username>
PW=<your-nassau-candy-pw>
```
</details>

## Getting started
1. Run the automation script.
```
python main.py
```