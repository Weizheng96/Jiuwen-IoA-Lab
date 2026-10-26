"""Discovery embedding adapter; the core never creates an agent."""
import httpx

class LLM:
    def __init__(self, settings):
        self.s = settings
        self.enabled = bool(settings.llm.get("api_key")) and not settings.stub

    def embed(self, texts):
        if not self.enabled:
            raise RuntimeError("Discovery embedding is not configured")
        response = httpx.post(self.s.llm["api_base"] + "/embeddings", trust_env=False, timeout=60,
                              headers={"Authorization":"Bearer " + self.s.llm["api_key"]},
                              json={"model":self.s.embedding_model,"input":texts})
        response.raise_for_status()
        return [item["embedding"] for item in response.json()["data"]]
