SHELL := /bin/bash
NAME := om-updater
VERSION := 1.0.5
TARBALL := $(NAME)-$(VERSION).tar.gz
BUILD_DIR := /tmp/$(NAME)-build-$(VERSION)
DIST_DIR := $(CURDIR)/dist

.PHONY: all clean tarball

all: tarball

tarball:
	@echo "Creating $(TARBALL)..."
	@rm -rf $(BUILD_DIR)
	@mkdir -p $(BUILD_DIR)/$(NAME)-$(VERSION)/icons
	@cp om-updater.py $(BUILD_DIR)/$(NAME)-$(VERSION)/
	@cp om-installer.py $(BUILD_DIR)/$(NAME)-$(VERSION)/
	@cp .abf.yml $(BUILD_DIR)/$(NAME)-$(VERSION)/
	@cp om-updater.spec $(BUILD_DIR)/$(NAME)-$(VERSION)/
	@cp icons/om-updater.png $(BUILD_DIR)/$(NAME)-$(VERSION)/icons/
	@cp icons/om-installer.png $(BUILD_DIR)/$(NAME)-$(VERSION)/icons/
	@mkdir -p $(DIST_DIR)
	@cd $(BUILD_DIR) && tar -czvf $(DIST_DIR)/$(TARBALL) $(NAME)-$(VERSION)
	@rm -rf $(BUILD_DIR)
	@sha512sum $(DIST_DIR)/$(TARBALL) | awk '{print $$1}'
	@echo "Created $(DIST_DIR)/$(TARBALL)"
	@echo "Update .abf.yml hash with the hash above"

clean:
	@rm -rf $(DIST_DIR)
	@rm -rf $(BUILD_DIR)
