"""A new key pair for locking client keys (admin app). Prints two .env lines and nothing else.

Run it yourself and paste the lines into .env (and the password manager's copy): the private half is the one secret
only the agent gets. Run where the project is installed, for example inside the agent container:
  docker compose -f compose.dev.yaml exec -T agent python - < scripts/make_key_pair.py
A new pair means every client key is entered again in the admin app.
"""

from support_agent.keys import make_key_pair

public, private = make_key_pair()
print(f"GHL_KEY_PUBLIC={public}")
print(f"GHL_KEY_PRIVATE={private}")
