


# Building_Muliti_DNN

This project focuses on building multi deep neural network using Nvidia NGC models of trafficnet and vehiclenet. Integrating it with Deepstream-6.1 pipeline the output will detect the vehicle and classify the make of it.



## Installation

Install the project 

```bash
  git clone https://github.com/DivyankaThakur03/building_multi_dnn_ds.git
  cd building_multi_dnn_ds
```

    
## Deployment

To deploy this project run

```bash
  python3 building_mdnn.py
```


## Documentation

I have downloaded the pretrained model from NVIDIA NGC catalog. 

Trafficcamnet-https://catalog.ngc.nvidia.com/orgs/nvidia/teams/tao/models/trafficcamnet  
This model detects 4 classes which include - car, bicycle, person, road_sign

Vehicletypenet-
https://catalog.ngc.nvidia.com/orgs/nvidia/teams/tao/models/vehicletypenet
The model described in this card is a classification network, which aims to classify car images into 6 vehicle types:
coupe, sedan, SUV, van, large vehicle,truck



