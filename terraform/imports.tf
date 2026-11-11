# One-time adoption of the objects `oc apply` already created, so the first apply updates them
# in place instead of failing with "already exists". Once they are in state these blocks do nothing.
# On a brand-new namespace, delete this file before the first apply (importing a missing object errors).

import {
  to = kubernetes_config_map_v1.config
  id = "${var.namespace}/openshift-node-app-config"
}

import {
  to = kubernetes_deployment_v1.app
  id = "${var.namespace}/openshift-node-app"
}

import {
  to = kubernetes_service_v1.app
  id = "${var.namespace}/openshift-node-app"
}
