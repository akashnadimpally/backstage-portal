variable "name" {
  description = "Name assigned to the module resource."
  type        = string
}

variable "tags" {
  description = "Tags or labels supplied by the consuming environment."
  type        = map(string)
  default     = {}
}