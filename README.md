# juju_team_status
Some tools to track bugs and other useful information

# Quickstart
1. Run collectors/collect_all.py to pull bugs from Launchpad into MongoDB
2. go run web_server.go

collect_all.py will keep running and refresh bugs from Launchpad every 5 minutes.
The web UI will automatically update with any changes.
