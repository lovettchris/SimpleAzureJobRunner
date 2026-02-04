# SimpleAzureJobRunner
This is a low-tech Azure job runner written entirely in python.  It supports submitting jobs to a pool of VM's that you created each running an instance of the job runner, where it coordinates which jobs are running on which VM through an Azure Storage table. It supports both Linux and Windows VM types.
