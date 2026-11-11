terraform {
  required_version = ">= 1.6"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 3.0, < 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5, < 4.0"
    }
  }
}

# Auth comes from the kubeconfig that `oc login` writes, so no token is ever stored in this folder.
# The context is pinned: ~/.kube/config also holds other clusters, and a plain apply must not
# land on whichever one happens to be current.
provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kube_context
}
