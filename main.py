import time

import tensorflow as tf

from pre_process import *
from sub_net import R_net, S_net

# Data path
train_path = "data/reviews_small.json"
embedding_path = "embedding/glove.twitter.27B.50d.txt"

# Model parameters
word_embedding_dim = 50  # set according to embedding_path
sent_length = 50  # length of a sentence
sequence_length = 500  # a input data dim, multiple of sent_length
learning_rate = 1e-5
batch_size = 32
rnn_dim = 64  # u, hidden layer size
k = 64  # k, hyper parameter for self-attention
training_epochs = 5  # training epochs

print("###### Load word embedding! ######")
embeddings, word_id = read_word_embedding(embedding_path, word_embedding_dim)
embedding_matrix = get_embedding_matrix(embeddings, word_embedding_dim)

print("###### Reading data! ######")
dataset = read_yelp_json(train_path)
train_count = int(len(dataset) * 0.9)
RUIs, RUs, RIs, yUIs = get_training_data(dataset[:train_count], word_id, sent_length, sequence_length)
dev_RUIs, dev_RUs, dev_RIs, dev_yUIs = get_training_data(dataset[train_count:], word_id, sent_length, sequence_length)

print("###### Model forward! ######")
RUI_batch = tf.placeholder(tf.int32, shape=(None, sequence_length), name="user_reviews_for_item_i")
RU_batch = tf.placeholder(tf.int32, shape=(None, sequence_length), name="user_reviews")
RI_batch = tf.placeholder(tf.int32, shape=(None, sequence_length), name="item_reviews")
label_batch = tf.placeholder(tf.int32, shape=(None,), name="label_batch")
in_batch_size = tf.shape(label_batch)[0]  # Actual inputting batch size
# Embedding, m = n = sequence_length
with tf.variable_scope("Embedding"):
    W = tf.Variable(tf.to_float(embedding_matrix), trainable=False, name="W")
    RUI_emb = tf.nn.embedding_lookup(W, RUI_batch, name="RUI_lookup")
    RU_emb = tf.nn.embedding_lookup(W, RU_batch, name="RU_lookup")
    RI_emb = tf.nn.embedding_lookup(W, RI_batch, name="RI_lookup")  # shape(in_batch_size,m,word_dim)

# Review Network | R-net, H shape(bs,2u,m), a shape=(bs,m), r shape=(bs,2u)
HU, HI, aUI_fw, aUI_bw, rUI_fw, rUI_bw = R_net(RU_emb, RI_emb, rnn_dim, batch_size, sequence_length, "R-net")

# Review Network | S-Net, S shape=(batch_size,2u)
S_U = S_net(HU, aUI_bw, rnn_dim, in_batch_size, sent_length, k, "S-net-RU")
S_I = S_net(HI, aUI_fw, rnn_dim, in_batch_size, sent_length, k, "S-net-RI")

# Review Network | Textual Matching
with tf.variable_scope("Textual_Matching"):
    xUI_bw = tf.concat([rUI_bw, S_U], axis=1)
    xUI_fw = tf.concat([rUI_fw, S_I], axis=1)  # shape=(in_batch_size,4u)

    W_bw = tf.get_variable('W_bw', (2 * rnn_dim, 4 * rnn_dim), initializer=tf.truncated_normal_initializer(stddev=0.1))
    W_fw = tf.get_variable('W_fw', (2 * rnn_dim, 4 * rnn_dim), initializer=tf.truncated_normal_initializer(stddev=0.1))
    xT_bw = tf.matmul(tf.tile(tf.expand_dims(W_bw, 0), (in_batch_size, 1, 1)), tf.expand_dims(xUI_bw, 2))
    xT_fw = tf.matmul(tf.tile(tf.expand_dims(W_fw, 0), (in_batch_size, 1, 1)), tf.expand_dims(xUI_fw, 2))
    xT = tf.tanh(tf.squeeze(xT_bw + xT_fw, [2]))  # shape=(in_batch_size,2u)

# Review Network and Visual Network | Fusion for Rating
with tf.variable_scope("FusionR"):
    W = tf.get_variable('W', (5, 6 * rnn_dim), initializer=tf.truncated_normal_initializer(stddev=0.1))
    b = tf.get_variable('b', (5,), initializer=tf.truncated_normal_initializer(stddev=0.1))
    W_expand = tf.tile(tf.expand_dims(W, 0), (in_batch_size, 1, 1))
    b_expand = tf.tile(tf.expand_dims(b, 0), (in_batch_size, 1))
    xV_p = tf.zeros((in_batch_size, 2 * rnn_dim))  # todo 临时代替Visual Network的输出
    xV_n = tf.zeros((in_batch_size, 2 * rnn_dim))
    x = tf.concat([xT, xV_p, xV_n], axis=1)  # shape=(in_batch_size,6u)
    y_sm = tf.nn.softmax(tf.squeeze(tf.matmul(W_expand, tf.expand_dims(x, 2)), [2]) + b_expand)

# Loss function and Optimizer
label_one_hot = tf.to_float(tf.one_hot(label_batch - tf.ones(tf.shape(label_batch), dtype=tf.int32), depth=5))
loss = tf.reduce_sum(tf.reduce_mean(tf.square(label_one_hot - y_sm), axis=1))
optimizer = tf.train.AdamOptimizer(learning_rate).minimize(loss)

# Session
with tf.Session() as sess:
    tf.device("/gpu:1")
    sess.run(tf.global_variables_initializer())
    print("###### Training begins! ######")
    clock1 = time.clock()
    batch_count = len(yUIs) // batch_size
    for epoch in range(training_epochs):
        for i in range(batch_count):
            start = i * batch_size
            end = start + batch_size
            feed = {RUI_batch: RUIs[start:end], RU_batch: RUs[start:end],
                    RI_batch: RIs[start:end], label_batch: yUIs[start:end]}
            result = sess.run([optimizer, loss], feed_dict=feed)
            real_loss = result[1]
            print("Epoch:%5d/%d | batch: %5d/%d, loss:%9f, time used:%5ds" %
                  (epoch + 1, training_epochs, i + 1, batch_count, real_loss, time.clock() - clock1))
    print("###### Testing begins! ######")
    test_count = len(dev_yUIs)
    correct = 0
    for i in range(test_count):
        feed = {RUI_batch: dev_RUIs[i:i + 1], RU_batch: dev_RUs[i:i + 1],
                RI_batch: dev_RIs[i:i + 1], label_batch: dev_yUIs[i:i + 1]}
        result = sess.run(y_sm, feed_dict=feed)
        y_pred = np.argmax(np.squeeze(result)) + 1
        if y_pred == dev_yUIs[i]:
            correct += 1
    print("Test count: %5d, correct: %5d, accuracy: %.4f%%" % (test_count, correct, correct / test_count * 100))