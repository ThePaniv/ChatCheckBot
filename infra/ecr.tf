# Chicken-and-egg: the Lambda functions need an image in this repo to be
# created. First run: terraform apply -target=aws_ecr_repository.bot,
# push the image, then run a full apply.

resource "aws_ecr_repository" "bot" {
  name = var.ecr_repo_name

  # The image is rebuilt from source on every deploy, so a teardown that also
  # clears the stored images (rather than erroring on a non-empty repo) is the
  # behavior we want.
  force_delete = true
}
