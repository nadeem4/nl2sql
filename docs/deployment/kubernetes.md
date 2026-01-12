# Kubernetes Deployment

For production environments, we recommend deploying on Kubernetes (EKS, GKE, AKS).

## 1. ConfigMap (Policies)

Mount your RBAC policies and Datasource configurations.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: nl2sql-config
data:
  policies.json: |
    {
      "admin": { "allowed_datasources": ["*"] }
    }
  datasources.yaml: |
    datasources:
      - id: "prod_db"
        connection: ...
```

## 2. Secrets

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: nl2sql-secrets
type: Opaque
data:
  OPENAI_API_KEY: <base64-encoded-key>
```

## 3. Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nl2sql
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nl2sql
  template:
    metadata:
      labels:
        app: nl2sql
    spec:
      containers:
        - name: core
          image: ghcr.io/nl2sql/platform:latest
          ports:
            - containerPort: 8000
          env:
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: nl2sql-secrets
                  key: OPENAI_API_KEY
          volumeMounts:
            - name: config-volume
              mountPath: /app/configs
      volumes:
        - name: config-volume
          configMap:
            name: nl2sql-config
```

## 4. Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: nl2sql-service
spec:
  selector:
    app: nl2sql
  ports:
    - protocol: TCP
      port: 80
      targetPort: 8000
  type: ClusterIP
```
