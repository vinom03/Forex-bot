name: Telegram Forwarder Bot

on:
  workflow_dispatch:
  schedule:
    - cron: '*/5 * * * *'

concurrency:
  group: telegram-forwarder-bot
  cancel-in-progress: false

jobs:
  run-bot:
    runs-on: ubuntu-latest

    permissions:
      contents: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests beautifulsoup4 deep-translator

      - name: Run bot
        env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
        run: python telegram_forwarder_fixed.py

      - name: Save last seen post id and log back to the repo
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          touch last_seen_id.txt bot_log.txt id_map.json
          git add last_seen_id.txt bot_log.txt id_map.json
          git diff --staged --quiet || git commit -m "Update bot state and logs"
          git push
