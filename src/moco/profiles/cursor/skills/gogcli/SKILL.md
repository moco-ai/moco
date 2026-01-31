---
name: gogcli
description: Google Suite CLI tool
version: 2.0.0
tools:
  - name: gmail_search
    command: gog gmail search {{query}} --limit={{limit}} --format=json
  - name: gmail_send
    command: gog gmail send --to={{to}} --subject={{subject}} --body={{body}}
  - name: calendar_list
    command: gog calendar list --format=json
  - name: drive_list
    command: gog drive list --format=json

