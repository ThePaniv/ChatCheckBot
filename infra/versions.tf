terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  # State is local and git-ignored (it can contain sensitive values). For a
  # solo project that's fine; switch to an S3 backend here if this ever grows.
}
