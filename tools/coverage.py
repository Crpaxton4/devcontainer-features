import subprocess


def main():
    subprocess.run(["coverage", "run", "-m", "unittest"])
    subprocess.run(["coverage", "report"])


if __name__ == "__main__":
    main()
