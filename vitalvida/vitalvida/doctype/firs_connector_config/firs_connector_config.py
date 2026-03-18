from frappe.model.document import Document
class FIRSConnectorConfig(Document):
    def get_active_credentials(self):
        if self.environment == "Production":
            return {
                "api_url": self.production_api_url,
                "api_key": self.get_password("production_api_key"),
                "api_secret": self.get_password("production_api_secret"),
            }
        return {
            "api_url": self.sandbox_api_url,
            "api_key": self.get_password("sandbox_api_key"),
            "api_secret": self.get_password("sandbox_api_secret"),
        }
