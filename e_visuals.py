import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def save_bar(data: dict, title: str, outpath: str):
    keys = list(data.keys())
    vals = [data[k] for k in keys]
    plt.figure()
    plt.bar(keys, vals)
    plt.title(title)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()

def save_line(y, title: str, outpath: str, xlabel="Message index", ylabel="Value"):
    plt.figure()
    plt.plot(range(len(y)), y)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()


