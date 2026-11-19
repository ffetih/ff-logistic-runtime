{{/* Name of every object: the release name, unless nameOverride is set. */}}
{{- define "ff-service.name" -}}
{{- default .Release.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels that never change for a service: the Deployment selector and the Service selector. */}}
{{- define "ff-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ff-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ff-service.labels" -}}
{{ include "ff-service.selectorLabels" . }}
app.kubernetes.io/part-of: ff-logistics
app.kubernetes.io/version: {{ .Values.image.tag | trunc 63 | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}
