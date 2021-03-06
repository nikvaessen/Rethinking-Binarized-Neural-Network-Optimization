import larq as lq
import tensorflow as tf
import tensorflow.keras as tk

from scipy.stats import f_oneway

from typing import List

from time import time


def get_cifar_data():
    (train_images, train_labels), (
        test_images,
        test_labels,
    ) = tk.datasets.cifar10.load_data()

    train_images = train_images.reshape((50000, 32, 32, 3))
    test_images = test_images.reshape((10000, 32, 32, 3))

    # Normalize pixel values to be between -1 and 1
    train_images, test_images = train_images / 127.5 - 1, test_images / 127.5 - 1

    return (train_images, train_labels), (test_images, test_labels)


def build_model(
    use_binary_weights=True, only_train_bm_layers=False, use_bm_layers=False
):
    kwargs = dict(
        input_quantizer="ste_sign",
        kernel_quantizer="ste_sign" if use_binary_weights else None,
        kernel_constraint="weight_clip",
        trainable=not only_train_bm_layers,
    )

    model = tk.models.Sequential()

    model.add(
        lq.layers.QuantConv2D(
            32,
            (3, 3),
            kernel_quantizer="ste_sign",
            kernel_constraint="weight_clip",
            use_bias=False,
            input_shape=(32, 32, 3),
            trainable=not only_train_bm_layers,
        )
    )
    model.add(tk.layers.MaxPooling2D((2, 2)))
    model.add(tk.layers.BatchNormalization(scale=False)) if use_bm_layers else None

    model.add(lq.layers.QuantConv2D(64, (3, 3), use_bias=False, **kwargs))
    model.add(tk.layers.MaxPooling2D((2, 2)))
    model.add(tk.layers.BatchNormalization(scale=False)) if use_bm_layers else None

    model.add(lq.layers.QuantConv2D(64, (3, 3), use_bias=False, **kwargs))
    model.add(tk.layers.BatchNormalization(scale=False)) if use_bm_layers else None

    model.add(tk.layers.Flatten())

    model.add(lq.layers.QuantDense(64, use_bias=False, **kwargs))
    model.add(tk.layers.BatchNormalization(scale=False)) if use_bm_layers else None
    model.add(lq.layers.QuantDense(10, use_bias=False, **kwargs))
    model.add(tk.layers.BatchNormalization(scale=False)) if use_bm_layers else None
    model.add(tk.layers.Activation("softmax"))

    opt = tk.optimizers.Adam()

    model.compile(
        optimizer=opt, loss="sparse_categorical_crossentropy", metrics=["accuracy"]
    )

    return model


def train_model(model, epochs=10):
    (train_images, train_labels), _ = get_cifar_data()

    tb = tk.callbacks.TensorBoard(
        log_dir="./approx_mnist/experiment__" + str(time()),
        histogram_freq=0,
        write_graph=True,
    )

    model.fit(
        train_images,
        train_labels,
        batch_size=64,
        epochs=epochs,
        verbose=1,
        callbacks=[tb],
    )


def test_model(model):
    _, (test_images, test_labels) = get_cifar_data()

    test_loss, test_acc = model.evaluate(test_images, test_labels, verbose=False)

    return test_acc, test_loss


def are_layers_equal(m1: tk.Model, m2: tk.Model, ignore_bm=False):
    m1_layers: List[tk.layers.Layer] = m1.layers
    m2_layers: List[tk.layers.Layer] = m2.layers

    if len(m1_layers) is not len(m2_layers):
        print("models layer length not equal")
        return False

    for l1, l2 in zip(m1_layers, m2_layers):
        if len(l1.weights) is not len(l2.weights):
            print("model weight length not equal")
            return False

        if "batch_normalization" in l1.name and ignore_bm:
            continue

        for w1, w2 in zip(l1.weights, l2.weights):
            if not tf.reduce_all(w1 == w2):
                print("weights not equal")
                return False

    return True


def main(with_bm=True):
    binary_weight_model: tk.Model = build_model(
        use_binary_weights=True, use_bm_layers=with_bm
    )

    # Train a model with binary weights
    train_model(binary_weight_model, epochs=6)

    # Create an equal model which will use real-valued weights
    real_weight_model: tk.Model = build_model(
        use_binary_weights=False, only_train_bm_layers=True, use_bm_layers=with_bm
    )
    real_weight_model.set_weights(binary_weight_model.get_weights())

    print(
        f"models have same weight: {are_layers_equal(binary_weight_model, real_weight_model, ignore_bm=True)}"
    )

    # Compare accuracies between both models
    binary_model_acc, _ = test_model(binary_weight_model)
    real_model_acc, _ = test_model(real_weight_model)

    # Retrain the batch normalization weights of the real-weighted model
    train_model(real_weight_model, epochs=6)

    print(
        f"models have same weight after retraining: {are_layers_equal(binary_weight_model, real_weight_model, ignore_bm=True)}"
    )

    real_model_retrained_acc, _ = test_model(real_weight_model)

    # set the retrained batch norm layers again on the binary network
    binary_weight_model.set_weights(real_weight_model.get_weights())

    binary_weight_model_acc_retrained = test_model(binary_weight_model)

    print(f"binary model accuracy: {binary_model_acc:.2f}")
    print(f"real model accuracy: {real_model_acc:.2f}")
    print(f"real model retrained accuracy: {real_model_retrained_acc:.2f}")
    print(f"binary model retrained accuracy: {binary_weight_model_acc_retrained}")

    return binary_model_acc, real_model_acc, real_model_retrained_acc


def result_stats():
    # copied from results.txt
    res = [
        (0.5922, 0.1013, 0.5976),
        (0.5778, 0.1, 0.5897),
        (0.5561, 0.1, 0.588),
        (0.5657, 0.0879, 0.5985),
        (0.5557, 0.1168, 0.5905),
        (0.5838, 0.1, 0.5868),
        (0.5765, 0.0917, 0.6013),
        (0.5862, 0.1, 0.5939),
        (0.5863, 0.1, 0.6009),
        (0.5504, 0.0802, 0.5948),
    ]

    bin = []
    real = []
    real_retrained = []

    for r in res:
        bin += [r[0]]
        real += [r[1]]
        real_retrained += [r[2]]

    import numpy as np

    bin = np.array(bin)
    real = np.array(real)
    real_retrained = np.array(real_retrained)

    f, pvalue = f_oneway(bin, real, real_retrained)

    print("binary")
    print(np.mean(bin))
    print(np.std(bin))

    print("latent")
    print(np.mean(real))
    print(np.std(real))

    print("latent retrained")
    print(np.mean(real_retrained))
    print(np.std(real_retrained))

    print("significant")
    print("f", f, "p", pvalue)


if __name__ == "__main__":
    # results = []
    #
    # for i in range(0, 10):
    #     results.append(main(with_bm=True))
    #
    # print(results)
    #
    # with open("approx_cifar/results.txt", "w") as f:
    #     f.write(str(results))

    # after results have been obtained
    result_stats()
