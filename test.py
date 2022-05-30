import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
import pennylane as qml
import matplotlib.pyplot as plt
from dataset16 import load_dataset as ld_full
from dataset_muon import load_dataset as ld_muon
from functools import partial
import jax
from jax.example_libraries.optimizers import adam
import jax.numpy as jnp

def QuantumModel(SEED, TRAIN_SIZE, TEST_SIZE, N_QUBITS, N_LAYERS, LR, N_EPOCHS):
  device = qml.device("default.qubit.jax", wires=N_QUBITS,prng_key = jax.random.PRNGKey(SEED))
  train_features,train_target,test_features,test_target = ld_full(TRAIN_SIZE,TEST_SIZE,SEED)
  train_features = jnp.array(train_features)
  train_target = jnp.array(train_target)
  test_features = jnp.array(test_features)
  test_target = jnp.array(test_target)

  @partial(jax.vmap,in_axes=[0,None])
  @qml.qnode(device,interface='jax')
  def circuit(x,w):
    qml.AngleEmbedding(x,wires=range(N_QUBITS))
    qml.StronglyEntanglingLayers(w,wires=range(N_QUBITS))
    return qml.expval(qml.PauliZ(0))

  def loss_fn(w,x,y):
    pred = circuit(x,w)
    return jnp.mean((pred - y) ** 2)
  
  def acc_fn(w,x,y):
    pred = circuit(x,w)
    return jnp.mean(jnp.sign(pred) == y)
  
  @jax.jit
  def train_step(stepid, opt_state,x,y):
    current_w = get_params(opt_state);
    loss_value, grads = jax.value_and_grad(loss_fn, argnums=0)(current_w,x,y)
    acc_value = acc_fn(current_w,x,y)
    opt_state = opt_update(stepid, grads, opt_state)
    return loss_value,acc_value, opt_state
  
  @jax.jit
  def test_step(stepid, opt_state, x, y):
    weights = get_params(opt_state)
    loss_value, grads = jax.value_and_grad(loss_fn, argnums=0)(weights, x, y)
    acc_value = acc_fn(weights,x,y)
    return loss_value, acc_value
  
  # Function that splits a dataset for stochastic method
  def split(data, rows):
    depth = len(data) // rows
    dataframes = jnp.split(data, depth)
    return dataframes, depth
  
  # Split the training dataet 
  depth = 250
  train_dataframe, chunks = split(train_features, depth)
  train_target_dataframe, chunks = split(train_target, depth)
  test_dataframe, chunks = split(test_features, depth)
  test_target_dataframe, chunks = split(test_target, depth)

  # Setting the inital weights
  weights = jax.random.uniform(jax.random.PRNGKey(SEED), (N_LAYERS, N_QUBITS, 3))*jax.numpy.pi

  opt_init, opt_update, get_params = adam(LR)
  opt_state = opt_init(weights)

  #----- Training ------#
  train_loss_data = np.zeros(N_EPOCHS)
  train_acc_data = np.zeros(N_EPOCHS)
  ep = jnp.linspace(0,N_EPOCHS, num=N_EPOCHS)
  
  print("Training Model....")
  print("Epoch\tLoss\tAccuracy")
  for i in range(N_EPOCHS):
    loss_temp = np.zeros(chunks)
    acc_temp = np.zeros(chunks)
    for j in range(chunks):
      loss_value,acc_value, opt_state = train_step(i,opt_state,train_dataframe[j],train_target_dataframe[j])
      loss_temp[j] = loss_value
      acc_temp[j] = acc_value
    train_loss_data[i] = jnp.average(loss_temp)
    train_acc_data[i] = jnp.average(acc_temp)
    if (i+1) % 100 == 0:
      print(f"{i+1}\t{loss_value:.3f}\t{acc_value*100:.2f}%")
  final_state = opt_state
  
  #------- Testing -------#

  print("Testing Model....")
  print("\tLoss\tAccuracy")
  loss_temp = np.zeros(chunks)
  acc_temp = np.zeros(chunks)
  for j in range(chunks):
    loss_value,acc_value = test_step(i,final_state,test_dataframe[j], test_target_dataframe[j])
    loss_temp[j] = loss_value
    acc_temp[j] = acc_value
  test_loss_data = jnp.average(loss_temp)
  test_acc_data = jnp.average(acc_temp)
  print(f"\t{loss_value:.3f}\t{acc_value*100:.2f}%")

  return train_loss_data, train_acc_data, test_loss_data, test_acc_data, ep

SEED=0      
TRAIN_SIZE = 2000 
TEST_SIZE = 2000
N_QUBITS = 16   
N_LAYERS = 2
LR=1e-3 
N_EPOCHS = 1000

train_layers_data = np.zeros(10)
test_layers_data = np.zeros(10)
num_layer = np.linspace(1,10, num =10)
for i in range(10):
  train_ld, train_ad, test_ld, test_ad, ep = QuantumModel(SEED, TRAIN_SIZE, TEST_SIZE, N_QUBITS, i, LR, N_EPOCHS)
  train_layers_data[i] = jnp.average(train_ad)
  test_layers_data[i] = test_ad[-1]
plt.title('Accuracy vs Layers')
plt.xlabel("# of layers", sie=14)
plt.ylabel('Accuracy', size=14)
plt.plot(num_layer,train_layers_data,'r',label='Training')
plt.plot(num_layer,test_layers_data,'b', label='Testing')
plt.legend(loc='lower right')
file_name = 'full_training'+str(TRAIN_SIZE)+'_testing'+str(TEST_SIZE)+'.png'
plt.savefig(file_name)
