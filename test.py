import gym
import numpy as np

from copy import copy
from collections import deque

from scheduler import *
from heurestics import *
from time_buffer import *
from score_buffer import *

bench_episodes = 50

def sample(rbf, size):
    indices = np.random.randint(len(rbf), size = size)
    batch = [rbf[index] for index in indices]

    states, actions, rewards, nstates, dones = [
            np.array(
                    [experience[field_index] for experience in batch]
            )
    for field_index in range(5)]

    return states, actions, rewards, nstates, dones

def train(tf, model, rbf, batch_size, loss_ftn, optimizer, gamma, nouts):
    states, actions, rewards, nstates, dones = sample(rbf, batch_size)

    next_Qvs = model(nstates)
    max_next_Qvs = np.max(next_Qvs, axis = 1)

    tgt_Qvs = (rewards + (1 - dones) * gamma * max_next_Qvs)
    tgt_Qvs = tgt_Qvs.reshape(-1, 1)

    mask = tf.one_hot(actions, nouts)
    with tf.GradientTape() as tape:
        all_Qvs = model(states)
        Qvs = tf.reduce_sum(all_Qvs * mask, axis = 1, keepdims = True)
        loss = tf.reduce_mean(loss_ftn(tgt_Qvs, Qvs))

    grads = tape.gradient(loss, model.trainable_variables)
    optimizer.apply_gradients(zip(grads, model.trainable_variables))

# If a skeleton becomes necessary use a list of some named tuples, or smthing
def run_tutoring(ename, skeleton, heurestic, schedref, trial, episodes, steps):
    import tensorflow as tf

    from tensorflow import keras

    # Config GPUs (consume only half the memory)
    gpus = tf.config.experimental.list_physical_devices('GPU')
    if gpus:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)

    # First two layers
    first_layer = keras.layers.Dense(skeleton[2][0], activation = skeleton[2][1], input_shape = skeleton[0])
    last_layer = keras.layers.Dense(skeleton[1])

    # Populating the layers
    layers = [first_layer]
    for sl in skeleton[3:]:
        layers.append(keras.layers.Dense(sl[0], activation = sl[1]))
    layers.append(last_layer)

    # Creating the models
    m1 = keras.models.Sequential(layers)
    m2 = keras.models.Sequential(layers)

    # Other variables
    env1 = gym.make(ename)
    env2 = gym.make(ename)

    eps = 1
    keps = (1 - eps)/2

    rbf1 = deque(maxlen = 2000)  # Should be customizable
    rbf2 = deque(maxlen = 2000)  # Should be customizable

    batch_size = 32             # Should be customizable
    scheduler = copy(schedref)

    gamma = 0.95                # Should be customizable
    loss_ftn = keras.losses.mean_squared_error
    optimizer = keras.optimizers.Adam(learning_rate = 1e-2)
    nouts = env1.action_space.n

    start = time.time()

    scores1 = []
    scores2 = []

    buf1 = ScoreBuffer()
    buf2 = ScoreBuffer()

    epsilons = []
    kepsilons = []

    id = ename + ': Tutoring with ' + scheduler.name + ': ' + str(trial)

    # Training loop
    tb = TimeBuffer(episodes)
    proj = 0

    for e in range(episodes):
        # Get the first observation
        state1 = env1.reset()
        state2 = env2.reset()

        score1 = 0
        score2 = 0

        done1 = False
        done2 = False

        tb.start()

        for s in range(steps):
            # TODO: function
            r = np.random.rand()
            if r < eps:
                action1 = heurestic(state1)
                action2 = heurestic(state2)
                pass
            elif r < eps + keps:    # Teacher-student or peer-peer here
                if buf1.average() > buf2.average():
                    model = m1
                else:
                    model = m2

                Qvs1 = model(state1[np.newaxis])
                Qvs2 = model(state2[np.newaxis])

                action1 = np.argmax(Qvs1[0])
                action2 = np.argmax(Qvs2[0])
            else:
                Qvs1 = m1(state1[np.newaxis])
                Qvs2 = m2(state2[np.newaxis])

                action1 = np.argmax(Qvs1[0])
                action2 = np.argmax(Qvs2[0])

            # Apply the action and update the state (TODO: another function)
            if not done1:
                nstate1, reward1, done1, info = env1.step(action1)
                rbf1.append((state1, action1, reward1, nstate1, done1))
                state1 = nstate1
                score1 += reward1

            if not done2:
                nstate2, reward2, done2, info = env2.step(action2)
                rbf2.append((state2, action2, reward2, nstate2, done2))
                state2 = nstate2
                score2 += reward2

            if done1 and done2:
                break

        # Post episode routines
        tb.split()
        proj, fmt1, fmt2 = cmp_and_fmt(proj, tb.projected())

        # Post processing
        scores1.append(score1)
        scores2.append(score2)

        buf1.append(score1)
        buf2.append(score2)

        # Logging (TODO: remove all score logging after debugging)
        msg = f'{id:<50} Episode {e:<5} Agent #1: Score = {score1:<5} [{buf1.average():.2f}]' \
                f' Agent #2: Score {score2:<5} [{buf2.average():.2f}] Epsilon {eps:.2f}' \
                f' Time = [{str(tb):<20}], Projected'

        print(msg + f' [{fmt1}]')
        # if e % notify.interval == 1:
        #    notify.log(msg + f' [{fmt2}]')

        epsilons.append(eps)
        kepsilons.append(keps)

        eps = scheduler()
        keps = (1 - eps)/2

        # Train the model if the rbf is full enough
        if len(rbf1) >= batch_size:
            train(tf, m1, rbf1, batch_size, loss_ftn, optimizer, gamma, nouts)

        if len(rbf2) >= batch_size:
            train(tf, m2, rbf2, batch_size, loss_ftn, optimizer, gamma, nouts)

    # Bench loop (TODO: another function)
    finals1 = []
    finals2 = []

    for e in range(bench_episodes):
        # Get the first observation
        state1 = env1.reset()
        state2 = env2.reset()

        score1 = 0
        score2 = 0

        done1 = False
        done2  = False

        for s in range(steps):
            # Get the action
            Qvs1 = m1(state1[np.newaxis])
            Qvs2 = m2(state2[np.newaxis])

            action1 = np.argmax(Qvs1[0])
            action2 = np.argmax(Qvs2[0])

            # Apply the action and update the state
            if not done1:
                nstate1, reward1, done1, info = env1.step(action1)
                state1 = nstate1
                score1 += reward1

            if not done2:
                nstate2, reward2, done2, info = env1.step(action2)
                state2 = nstate2
                score2 += reward2

            if done1 and done2:
                break

        finals1.append(score1)
        finals2.append(score2)

    # Log completion
    msg = f'{id} finished in {fmt_time(time.time() - start)}'
    print(msg)
    # notify.notify(msg)

    return scores1, scores2, epsilons, kepsilons, finals1, finals2

rets = run_tutoring('CartPole-v1', [[4], 2, [32, 'elu'], [32, 'elu']],
        Heurestic('Egreedy', egreedy), LinearDecay(800, 50),
        1, 1500, 500)
print(rets)
