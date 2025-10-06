{{- define "storefront.labels" -}}
app.kubernetes.io/name: storefront
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: storefront
{{- end -}}

{{- define "storefront.appSelector" -}}
app.kubernetes.io/name: storefront
app.kubernetes.io/component: storefront-web
{{- end -}}

{{/*
DSN for the app role, assembled from the CNPG-issued app secret at runtime.
We never put the password in a manifest — it is read from the secret via env.
*/}}
