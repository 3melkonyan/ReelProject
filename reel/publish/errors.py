"""Shared publishing failure contract; retries belong to the pipeline."""


class PublishError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args()
    error = PublishError("smoke", retryable=False)
    assert str(error) == "smoke" and error.retryable is False
    print("OK: PublishError (offline)")
