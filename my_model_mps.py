# Imports
import numpy as np
import pandas as pd
import os
import pennylane as qml
import matplotlib.pyplot as plt
from dataset16 import load_dataset as ld_full
from dataset_muon import load_dataset as ld_muon
from functools import partial
import jax
from jax.example_libraries.optimizers import adam
import sklearn
from sklearn.metrics import roc_curve, roc_auc_score 

# ------ Constants ------#

SEED=0      
TRAIN_SIZE = 10000 
TEST_SIZE = 5000
N_QUBITS = 16   
N_PARAMS_B = 3
LR=1e-2 
N_EPOCHS = 3000
BATCH_SIZE = 200

#------------------------#

# Definiton of the Pennylane device using JAX
device = qml.device("default.qubit.jax", wires=N_QUBITS,prng_key = jax.random.PRNGKey(SEED))

# The block defines a variational quantum circuit that takes the position of tensors in the circuit
def Block(weights,wires):
  qml.RZ(weights[0], wires=wires[0])
  qml.RY(weights[1], wires=wires[1])
  qml.U1(weights[2],wires=wires[0])
  qml.CZ(wires=wires)

# Definition of the quantum circuit
# x : features from the jet structure
# w : weights of the model
# The qml.qnode decorator transforms the python function into a Pennylane QNode
# i.e. a circuit to be run on a specified device.
# The partial(jax.vmap) decorator creates a vectorized version of the function
# This way I can process multiple jets at one time, passing a vector of features.
# in_axes = [0,None] specifies that I want to vectorize the function with respect
# to the first parameter only (x), since I want the weights (w) to be the same for
# each jet.
@partial(jax.vmap,in_axes=[0,None]) # Vectorized version of the function
@qml.qnode(device,interface='jax')  # Create a Pennylane QNode
def Circuit(x,w):
  qml.AngleEmbedding(x,wires=range(N_QUBITS))   # Features x are embedded in rotation angles
  qml.MPS(wires=range(N_QUBITS), n_block_wires=2,block=Block, n_params_block=N_PARAMS_B, template_weights=w) # Variational layer
  return qml.expval(qml.PauliZ(N_QUBITS-1)) # Expectation value of the \sigma_z operator on the 1st qubit

# Simple MSE loss function
def Loss(w,x,y):
  pred = Circuit(x,w)
  return jax.numpy.mean((pred - y) ** 2)

# Simple binary accuracy function
def Accuracy(w,x,y):
  pred = Circuit(x,w)
  return jax.numpy.mean(jax.numpy.sign(pred) == y)

# Weights are initialized randomly
weights = jax.random.uniform(jax.random.PRNGKey(SEED), (N_QUBITS-1, N_PARAMS_B))*jax.numpy.pi

# The ADAM optimizer is initialized
opt_init, opt_update, get_params = adam(LR)
opt_state = opt_init(weights)

# Training step
# This function is compiled Just-In-Time on the GPU
@jax.jit
def Train_Step(stepid, opt_state,train_f,train_t):
  current_w = get_params(opt_state)
  loss_value, grads = jax.value_and_grad(Loss,argnums=0)(current_w,train_f,train_t)
  acc_value = Accuracy(current_w,train_f,train_t)
  opt_state = opt_update(stepid, grads, opt_state)
  return loss_value,acc_value, opt_state

@jax.jit
def Test_Step(current_w,test_f,test_t):
  loss_value, grads = jax.value_and_grad(Loss,argnums=0)(current_w,test_f,test_t)
  acc_value = Accuracy(current_w,test_f,test_t)
  return loss_value, acc_value

def Batch_and_Shuffle(x,y):
  z = int(len(x) / BATCH_SIZE)
  data = np.column_stack([x,y])
  np.random.shuffle(data)
  return np.split(data[:,0:N_QUBITS],z), np.split(data[:,-1],z),z

def Train_Model(x, y):
  z = int(len(x)/BATCH_SIZE)
  loss_data = np.zeros(N_EPOCHS*z)
  acc_data = np.zeros(N_EPOCHS*z)
  print("Training...")
  print("Epoch\tLoss\tAccuracy")
  step=0
  for i in range(N_EPOCHS):
    # Batch and shuffle the data for ever epoch
    train_f, train_t, chunks = Batch_and_Shuffle(x, y)

    for j in range(chunks):
      loss_data[step],acc_data[step], opt_state = Train_Step(step, opt_state, train_f[j], train_t[j])
      step+=1

    if (i+1) % 100 == 0:
      print(f"{i+1}\t{loss_data[sgtep-1]:.3f}\t{acc_data[step-1]*100:.2f}%")
      np.save("mps_w/mps_weights_epcoh_"+ str(i+1) +".npy", get_params(opt_state))
   
  file_weights = "mps_w/final_mps_weights.npy"
  np.save(file_weights, get_params(opt_state))

  return opt_state, loss_data, acc_data

def Test_Model(w, x, y):
  print("Testing...")  
  print("\tLoss\tAccuracy")
  # Batch and shuffle the data for ever epoch
  test_f, test_t, chunks = Batch_and_Shuffle(x, y)
  loss_temp = np.zeros(chunks)
  acc_temp = np.zeros(chunks)

  for j in range(chunks):
    loss_temp[j],acc_temp[j] = Test_Step(w, test_f[j], test_t[j])

  loss_data = np.average(loss_temp)
  acc_data = np.average(acc_temp)

  print(f"\t{loss_data:.3f}\t{acc_data*100:.2f}%")

  return loss_data, acc_data

def Plot_ROC(w,x,y):
  depth = int(len(x) / BATCH_SIZE)
  new_x = np.split(x,depth)
  ps = np.array(TEST_SIZE)
  for i in range(depth):
    ps[i] = Circuit(new_x[i],w)
  predictions = np.reshape(ps, (ps.shape[0]*ps.shape[1], ps.shape[2])) # Convert 3D array to 2D array  
  fpr, tpr, threshold = roc_curve(y,predictions)
  auc = roc_auc_score(y,predictions)
  df_auc = np.ones(len(fpr))*auc
  
  # Get data predictions from the XGBoost to compare ROC curves
  xgb_csv =  pd.read_csv('/data/test_withxgb.csv')
  xgb_pred = xgb_csv['XGB_PRED'] 
  xgb_target = xgb_csv['Jet_LABEL']*2-1
  xgb_fpr,xgb_tpr,xgb_threshold = roc_curve(xgb_target,xgb_pred)
  xgb_auc = roc_auc_score(xgb_target,xgb_pred)
  
  plt.plot([0, 1], [0, 1], color="navy", linestyle="--")
  plt.plot(fpr,tpr,label="ROC QML,MPS(area = %0.2f)" % auc)
  plt.plot(xgb_fpr,xgb_tpr,label="ROC XGBoost(area = %0.2f)" % xgb_auc)
  plt.xlabel("False Positive Rate")
  plt.ylabel("True Positive Rate")
  plt.title("Receiver Operating Characteristic")
  plt.legend(loc="lower right")
  fname = 'ROC_mps_training' +str(TRAIN_SIZE)+'_testing'+str(TEST_SIZE)+'.png'
  plt.savefig(fname)
  plt.clf()
  
  roc_d = {'FPR': fpr, 'TPR': tpr, 'Threshold': threshold, 'Area': df_auc}
  frame = pd.DataFrame(roc_d)
  frame.to_csv('mps_roc_data.csv', index=False)

def Plot_Loss_and_Acc(ep,loss,acc):
  fig, ax1 = plt.subplots() 
  ax1.set_xlabel('# of Epochs') 
  ax1.set_ylabel('Loss', color = 'black') 
  plot_1 = ax1.plot(ep, loss, color = 'black') 
  ax1.tick_params(axis ='y', labelcolor = 'black')
  ax2 = ax1.twinx() 
  ax2.set_ylabel('Accuracy', color = 'green') 
  plot_2 = ax2.plot(ep, acc, color = 'green') 
  ax2.tick_params(axis ='y', labelcolor = 'green')
  plt.title("Matrix Product State Architecture Loss and Accuracy")
  file_name = 'mps_full_training'+str(TRAIN_SIZE)+'_testing'+str(TEST_SIZE)+'.png'
  plt.savefig(file_name) 

def Run_Model():
  # Loads the dataset (already preprocessed... see dataset.py)
  train_features,train_target,test_features,test_target = ld_full(TRAIN_SIZE,TEST_SIZE,SEED)

  path = "/home/leonidas/example-qml4btag/mps_w"
  weights_files = os.scandir(path) # Get the .npy weight files
  with weights_files as entries:
    for entry in entries:
        print(entry.name)
  print("Please Choose a file from above to load the weights for the MPS model, otherwise press the space bar, then enter to pass this stage.")
  w_f = input("Enter file name:  ")
  if (w_f != " "):
    weights = np.load(path+"/"+w_f)
    test_loss, test_acc = Test_Model(weights, test_features, test_target)
    Plot_ROC(weights,test_features,test_target)
  else:
    final_state, train_loss, train_acc = Train_Model(train_features, train_target)
    weights = get_params(final_state)
    ep = np.linspace(1,N_EPOCHS,num=N_EPOCHS)
    Plot_Loss_and_Acc(ep,train_loss,train_acc)
    
    test_loss, test_acc = Test_Model(weights, test_features, test_target)
    Plot_ROC(weights,test_features,test_target)
    
    d = {'Epochs': ep, 'Train Loss': train_loss, 'Train Accuracy':train_acc, 'Test Loss':test_loss, 'Test Accuracy':test_acc}
    frame = pd.DataFrame(d)
    frame.to_csv('mps_loss_accuracy_data', index=False)
    
