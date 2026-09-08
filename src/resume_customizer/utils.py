def read_file(file_name):
    with open(file_name, "r", encoding="utf-8") as f:
        latex = f.read()

    return latex