from src.connector import WhisperConnector


def main() -> None:
    connector = WhisperConnector()
    connector.start()


if __name__ == "__main__":
    main()
