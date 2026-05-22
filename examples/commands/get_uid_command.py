class GetUidCommand:
    def __init__(self, client):
        self.client = client

    def __call__(self) -> int:
        """Get the UID of the current user."""
        return self.client.uid
